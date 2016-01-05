import React from 'react';

export default React.createClass({

    displayName: 'ColorBy',

    propTypes: {
        dataset: React.PropTypes.object,
        name: React.PropTypes.string,
        noSolid: React.PropTypes.bool,
        onChange: React.PropTypes.func,
        value: React.PropTypes.string,
    },

    getDefaultProps() {
        return {
            dataset: { data: { arrays: [] }},
            name: 'ColorBy',
            noSolid: false,
            onChange: null,
            value: '__SOLID__',
        };
    },

    getInitialState() {
        return {
            value: this.props.value,
        };
    },

    updateColorBy(event) {
        if(this.props.onChange) {
            this.props.onChange(this.props.name, event.target.value);
        }
        this.setState({value: event.target.value});
    },

    render() {
        const otherList = [];
        if (!this.props.noSolid) {
            otherList.push(<option key='__SOLID__' value='__SOLID__'>Solid color</option>);
        }
        return  <select value={this.state.value} onChange={ this.updateColorBy }>
                    { otherList }
                    {  this.props.dataset.data.arrays.map(array => {
                        return <option key={array.name} value={array.name}>{array.label}</option>
                    })}
                </select>;
    },
});