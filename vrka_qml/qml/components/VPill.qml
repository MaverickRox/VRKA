pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
    id: root

    property string text: ""
    property color textColor: Theme.textDim
    property color pillColor: Theme.cardAlt
    property color borderColor: Theme.border
    property string iconSource: ""
    property int iconSize: 12

    implicitHeight: 24
    implicitWidth: contentRow.implicitWidth + 16
    radius: height / 2
    color: pillColor
    border.width: 1
    border.color: borderColor

    RowLayout {
        id: contentRow
        anchors.centerIn: parent
        spacing: 6

        Image {
            visible: root.iconSource !== ""
            source: root.iconSource
            Layout.preferredWidth: root.iconSize
            Layout.preferredHeight: root.iconSize
            fillMode: Image.PreserveAspectFit
            mipmap: true
        }

        Text {
            text: root.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.microSize
            font.bold: true
            color: root.textColor
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
